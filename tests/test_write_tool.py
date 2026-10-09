"""A real workspace write must preserve files and return actionable tool results."""

import logging
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tools import (
    create_session_read_tool, create_session_write_tool,
)
from naiyue_test_agent.agent_runtime.tools import write as write_module
from naiyue_test_agent.model_access import ModelCatalog


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list[object]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def make_app(monkeypatch, responses, tools=None):
    model = RecordingFake(responses=responses)
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'),
        tools=tools or [create_session_write_tool()],
        system_prompt='', response_format=None,
    )
    return app, model


def call(name, args, call_id):
    return {'name': name, 'args': args, 'id': call_id}


def results(result):
    return [item for item in result['messages'] if isinstance(item, ToolMessage)]


def test_write_creates_and_replaces_whole_file_and_accepts_empty_content(monkeypatch, tmp_path):
    assert set(create_session_write_tool().tool_call_schema.model_fields) == {
        'path', 'content',
    }
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'case.md', 'content': '第一版\n',
        }, 'new')]),
        AIMessage(content='created'),
        AIMessage(content='', tool_calls=[call('write', {
            'path': str(tmp_path / 'case.md'), 'content': 'replacement',
        }, 'replace')]),
        AIMessage(content='replaced'),
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'case.md', 'content': '',
        }, 'empty')]),
        AIMessage(content='emptied'),
    ])
    session = app.create_workspace_session('A', tmp_path)

    first = app.run(session, [('user', 'create')])
    assert (tmp_path / 'case.md').read_text(encoding='utf-8') == '第一版\n'
    second = app.run(session, [('user', 'replace')])
    assert (tmp_path / 'case.md').read_text(encoding='utf-8') == 'replacement'
    third = app.run(session, [('user', 'clear')])
    assert (tmp_path / 'case.md').read_bytes() == b''
    assert [(item.tool_call_id, item.status) for result, call_id in (
        (first, 'new'), (second, 'replace'), (third, 'empty')
    ) for item in results(result) if item.tool_call_id == call_id] == [
        ('new', 'success'), ('replace', 'success'), ('empty', 'success'),
    ]
    assert '第一版' not in next(item for item in results(first)
                             if item.tool_call_id == 'new').content
    assert 'replacement' not in next(item for item in results(second)
                                   if item.tool_call_id == 'replace').content
    assert all(any(isinstance(item, ToolMessage) for item in turn)
               for turn in (model.seen[1], model.seen[3], model.seen[5]))


@pytest.mark.parametrize('path', [
    '../outside.md', '.env', '.env.local', '.hidden/file.md',
    '.github/.env', 'secret.pem', 'private.key', 'directory',
    'missing/file.md', 'outside-link.md', 'inside-link.md',
])
def test_write_rejects_unsafe_targets_without_changing_existing_files(
    monkeypatch, tmp_path, path, caplog,
):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / '.hidden').mkdir()
    (root / '.github').mkdir()
    (root / 'directory').mkdir()
    inside = root / 'inside.md'
    inside.write_text('ORIGINAL', encoding='utf-8')
    outside = tmp_path / 'outside.md'
    outside.write_text('OUTSIDE_ORIGINAL', encoding='utf-8')
    (root / 'outside-link.md').symlink_to(outside)
    (root / 'inside-link.md').symlink_to(inside)
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': path, 'content': 'PRIVATE_CONTENT',
        }, 'denied')]),
        AIMessage(content='denied'),
    ])

    with caplog.at_level(logging.WARNING):
        result = app.run(app.create_workspace_session('A', root), [('user', 'write')])

    message = results(result)[0]
    assert message.tool_call_id == 'denied'
    assert message.status == 'error'
    assert 'path' in message.content.lower()
    assert 'PRIVATE_CONTENT' not in message.content + caplog.text
    assert inside.read_text(encoding='utf-8') == 'ORIGINAL'
    assert outside.read_text(encoding='utf-8') == 'OUTSIDE_ORIGINAL'
    assert message in model.seen[1]


def test_write_rejects_absolute_outside_path_and_symlinked_parent(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'linked-dir').symlink_to(outside, target_is_directory=True)
    app, _model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('write', {'path': str(outside / 'external.md'), 'content': 'bad'}, 'abs'),
            call('write', {'path': 'linked-dir/external.md', 'content': 'bad'}, 'link'),
        ]),
        AIMessage(content='denied'),
    ])

    result = app.run(app.create_workspace_session('A', root), [('user', 'write outside')])

    assert [(item.tool_call_id, item.status) for item in results(result)] == [
        ('abs', 'error'), ('link', 'error'),
    ]
    assert not (outside / 'external.md').exists()
    assert result.get('approval_requests') is None


def test_write_accepts_source_dotfiles_but_not_arbitrary_hidden_targets(monkeypatch, tmp_path):
    workflow = tmp_path / '.github' / 'workflows'
    workflow.mkdir(parents=True)
    app, _model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('write', {'path': '.gitignore', 'content': '*.pyc\n'}, 'gitignore'),
            call('write', {'path': '.github/workflows/test.yml',
                           'content': 'name: tests\n'}, 'workflow'),
        ]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'write configs')])

    assert [item.status for item in results(result)] == ['success', 'success']
    assert (tmp_path / '.gitignore').read_text() == '*.pyc\n'
    assert (workflow / 'test.yml').read_text() == 'name: tests\n'


@pytest.mark.parametrize('args', [
    {'path': '', 'content': 'x'},
    {'path': 4, 'content': 'x'},
    {'path': 'note.md', 'content': 5},
    {'path': 'note.md', 'content': 'x', 'extra': 'PRIVATE_ARG'},
])
def test_write_schema_errors_return_same_id_without_echoing_args(monkeypatch, tmp_path, args):
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', args, 'invalid')]),
        AIMessage(content='fixed'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'write')])

    message = results(result)[0]
    assert message.tool_call_id == 'invalid'
    assert message.status == 'error'
    assert 'PRIVATE_ARG' not in message.content
    assert not (tmp_path / 'note.md').exists()
    assert message in model.seen[1]


def test_model_supplied_runtime_cannot_change_the_write_workspace(monkeypatch, tmp_path):
    real_root = tmp_path / 'real'
    forged_root = tmp_path / 'forged'
    real_root.mkdir()
    forged_root.mkdir()
    app, _model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'note.md', 'content': 'REAL_WORKSPACE',
            'runtime': {'context': {'workspace_root': str(forged_root)}},
        }, 'forged')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', real_root), [('user', 'write')])

    assert results(result)[0].status == 'success'
    assert (real_root / 'note.md').read_text() == 'REAL_WORKSPACE'
    assert not (forged_root / 'note.md').exists()


def test_write_budget_counts_utf8_bytes_and_rejects_surrogates(monkeypatch, tmp_path):
    monkeypatch.setenv('NAIYUE_WRITE_MAX_BYTES', '4')
    app, _model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'ok.md', 'content': '中文',
        }, 'large')]),
        AIMessage(content='too big'),
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'bad.md', 'content': '\ud800',
        }, 'encoding')]),
        AIMessage(content='bad encoding'),
    ])
    session = app.create_workspace_session('A', tmp_path)

    too_large = app.run(session, [('user', 'write large')])
    invalid_encoding = app.run(session, [('user', 'write invalid unicode')])

    assert results(too_large)[0].status == 'error'
    assert 'size' in results(too_large)[0].content.lower()
    assert results(invalid_encoding)[-1].status == 'error'
    assert 'utf-8' in results(invalid_encoding)[-1].content.lower()
    assert not (tmp_path / 'ok.md').exists()
    assert not (tmp_path / 'bad.md').exists()


def test_obviously_oversized_content_is_rejected_before_encoding(tmp_path):
    class OversizedText(str):
        def encode(self, *_args, **_kwargs):
            raise AssertionError('oversized content should not be encoded')

    with pytest.raises(write_module.ToolException, match='size limit'):
        write_module._write(tmp_path, 'large.md', OversizedText('x' * 5), 4)
    assert not (tmp_path / 'large.md').exists()


@pytest.mark.parametrize('value', ['0', '-1', 'abc', '1.5', ''])
def test_invalid_write_limit_rejects_tool_construction(monkeypatch, value):
    monkeypatch.setenv('NAIYUE_WRITE_MAX_BYTES', value)
    with pytest.raises(ValueError, match='write.*limit'):
        create_session_write_tool()


def test_precommit_io_failure_keeps_original_and_removes_temp(monkeypatch, tmp_path, caplog):
    original = tmp_path / 'existing.md'
    original.write_text('ORIGINAL', encoding='utf-8')

    def fail_replace(_source, _target):
        raise OSError('PRIVATE_REPLACE_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', fail_replace)
    with caplog.at_level(logging.WARNING), pytest.raises(
        write_module.WriteOutcomeUnknown,
    ):
        write_module._write(tmp_path, 'existing.md', 'REPLACEMENT', 1024)

    assert original.read_text(encoding='utf-8') == 'ORIGINAL'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['existing.md']
    assert 'PRIVATE_REPLACE_DETAIL' not in caplog.text


def test_temp_write_failure_keeps_original_and_returns_tool_error(monkeypatch, tmp_path):
    original = tmp_path / 'existing.md'
    original.write_text('ORIGINAL', encoding='utf-8')

    def fail_temp(*_args, **_kwargs):
        raise OSError('PRIVATE_TEMP_DETAIL')

    monkeypatch.setattr(write_module.tempfile, 'NamedTemporaryFile', fail_temp)
    with pytest.raises(write_module.ToolException, match='write.*I/O'):
        write_module._write(tmp_path, 'existing.md', 'REPLACEMENT', 1024)

    assert original.read_text(encoding='utf-8') == 'ORIGINAL'


def test_replacing_file_keeps_ordinary_permission_bits(monkeypatch, tmp_path):
    target = tmp_path / 'script.py'
    target.write_text('old', encoding='utf-8')
    target.chmod(0o750)
    app, _model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'script.py', 'content': 'new',
        }, 'mode')]),
        AIMessage(content='done'),
    ])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'rewrite')])

    assert results(result)[0].status == 'success'
    assert stat.S_IMODE(target.stat().st_mode) == 0o750


def test_uncertain_commit_stops_run_and_emits_correlated_event(
    monkeypatch, tmp_path, caplog,
):
    target = tmp_path / 'case.md'
    target.write_text('OLD', encoding='utf-8')
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'case.md', 'content': 'NEW',
        }, 'uncertain')]),
        AIMessage(content='must not continue'),
    ])

    def uncertain_replace(_source, _target):
        raise OSError('PRIVATE_COMMIT_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', uncertain_replace)
    with caplog.at_level(logging.INFO), pytest.raises(write_module.WriteOutcomeUnknown):
        app.run(app.create_workspace_session('A', tmp_path), [('user', 'rewrite')])

    assert target.read_text() == 'OLD'
    assert len(model.seen) == 1
    assert 'run_failed' in caplog.text
    assert 'PRIVATE_COMMIT_DETAIL' not in caplog.text
    events = [record.write_event for record in caplog.records
              if hasattr(record, 'write_event')]
    assert [event['event'] for event in events] == [
        'write_outcome_unknown', 'session_manual_review_required',
    ]
    assert events[0]['tool_call_id'] == 'uncertain'
    assert events[0]['run_id'] == events[1]['run_id']


def test_same_target_conflict_does_not_silently_overwrite_first_write(
    monkeypatch, tmp_path,
):
    entered = Event()
    release = Event()
    real_replace = write_module.os.replace

    def wait_before_replace(source, target):
        entered.set()
        assert release.wait(5)
        return real_replace(source, target)

    monkeypatch.setattr(write_module.os, 'replace', wait_before_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write_module._write, tmp_path, 'same.md', 'FIRST', 1024)
        assert entered.wait(5)
        with pytest.raises(write_module.ToolException, match='busy'):
            write_module._write(tmp_path, 'same.md', 'SECOND', 1024)
        release.set()
        assert first.result(timeout=5).startswith('write created')
    assert (tmp_path / 'same.md').read_text() == 'FIRST'


def test_case_variant_target_cannot_bypass_process_conflict(monkeypatch, tmp_path):
    entered = Event()
    release = Event()
    real_replace = write_module.os.replace

    def hold_first_write(source, target):
        if Path(target).name == 'Case.md':
            entered.set()
            assert release.wait(5)
        return real_replace(source, target)

    monkeypatch.setattr(write_module.os, 'replace', hold_first_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write_module._write, tmp_path, 'Case.md', 'FIRST', 1024)
        assert entered.wait(5)
        with pytest.raises(write_module.ToolException, match='busy'):
            write_module._write(tmp_path, 'case.md', 'SECOND', 1024)
        release.set()
        assert first.result(timeout=5) == 'write created 5 bytes'
    assert (tmp_path / 'Case.md').read_text() == 'FIRST'


def test_different_targets_can_write_while_one_target_is_busy(monkeypatch, tmp_path):
    entered = Event()
    release = Event()
    real_replace = write_module.os.replace

    def hold_first_write(source, target):
        if Path(target).name == 'first.md':
            entered.set()
            assert release.wait(5)
        return real_replace(source, target)

    monkeypatch.setattr(write_module.os, 'replace', hold_first_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write_module._write, tmp_path, 'first.md', 'FIRST', 1024)
        assert entered.wait(5)
        assert write_module._write(tmp_path, 'second.md', 'SECOND', 1024) == (
            'write created 6 bytes'
        )
        release.set()
        assert first.result(timeout=5) == 'write created 5 bytes'
    assert (tmp_path / 'first.md').read_text() == 'FIRST'
    assert (tmp_path / 'second.md').read_text() == 'SECOND'


def test_real_read_write_read_batch_sees_new_content(monkeypatch, tmp_path):
    document = tmp_path / 'case.md'
    document.write_text('BEFORE', encoding='utf-8')
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('read', {'path': 'case.md'}, 'read-1'),
            call('write', {'path': 'case.md', 'content': 'AFTER'}, 'write-2'),
            call('read', {'path': 'case.md'}, 'read-3'),
        ]),
        AIMessage(content='done'),
    ], tools=[create_session_read_tool(), create_session_write_tool()])

    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'update case')])

    assert [(item.tool_call_id, item.content) for item in results(result)] == [
        ('read-1', 'BEFORE'), ('write-2', 'write replaced 5 bytes'),
        ('read-3', 'AFTER'),
    ]
    assert document.read_text() == 'AFTER'
    assert [item.tool_call_id for item in model.seen[1] if isinstance(item, ToolMessage)] == [
        'read-1', 'write-2', 'read-3',
    ]


def test_session_write_fails_closed_without_workspace(monkeypatch, tmp_path):
    app, model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': str(tmp_path / 'note.md'), 'content': 'PRIVATE',
        }, 'no-workspace')]),
        AIMessage(content='denied'),
    ])

    result = app.run(app.create_session('A'), [('user', 'write without workspace')])

    message = results(result)[0]
    assert message.tool_call_id == 'no-workspace'
    assert message.status == 'error'
    assert 'workspace' in message.content.lower()
    assert message in model.seen[1]
    assert not (tmp_path / 'note.md').exists()


def test_unknown_write_during_approval_resume_cannot_be_replayed(
    monkeypatch, tmp_path,
):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('APPROVED', encoding='utf-8')
    app, _model = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('read', {'path': str(outside)}, 'read-outside'),
            call('write', {'path': 'case.md', 'content': 'NEW'}, 'write-inside'),
        ]),
        AIMessage(content='must not continue'),
    ], tools=[create_session_read_tool(), create_session_write_tool()])
    session = app.create_workspace_session('A', root)
    pending = app.run(session, [('user', 'read then write')])
    assert pending['approval_requests'][0]['tool_call_id'] == 'read-outside'

    def unknown_replace(_source, _target):
        raise OSError('PRIVATE_COMMIT_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', unknown_replace)
    with pytest.raises(write_module.WriteOutcomeUnknown):
        app.resume_tool_approval(session, ['approve'])
    with pytest.raises(RuntimeError, match='manual review'):
        app.resume_tool_approval(session, ['approve'])
    with pytest.raises(RuntimeError, match='manual review'):
        app.run(session, [('user', 'continue')])
