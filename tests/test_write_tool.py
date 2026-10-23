"""A real workspace write must preserve files and return actionable tool results."""

from native_application_helpers import async_test, build_application

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
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import (
    WriteSettings, create_session_read_tool, create_session_write_tool,
)
from naiyue_test_agent.tools import write as write_module
from naiyue_test_agent.tools.paths import prepare_file_subject
from naiyue_test_agent.model_access import ModelCatalog


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list[object]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


async def make_app(monkeypatch, responses, tools=None, *, permissions=None):
    model = RecordingFake(responses=responses)
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'),
        tools=tools or [create_session_write_tool()],
        system_prompt='', response_format=None, settings=AppSettings.model_validate({'permissions': permissions or {}}),
    )
    return app, model


def call(name, args, call_id):
    return {'name': name, 'args': args, 'id': call_id}


def results(result):
    return [item for item in result['messages'] if isinstance(item, ToolMessage)]


@async_test
async def test_write_creates_and_replaces_whole_file_and_accepts_empty_content(monkeypatch, tmp_path):
    assert set(create_session_write_tool().tool.tool_call_schema.model_fields) == {
        'path', 'content',
    }
    app, model = await make_app(monkeypatch, [
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
    session = await app.sessions.create_workspace('A', tmp_path)

    first = await app.runs.start(session, [('user', 'create')])
    assert (tmp_path / 'case.md').read_text(encoding='utf-8') == '第一版\n'
    second = await app.runs.start(session, [('user', 'replace')])
    assert (tmp_path / 'case.md').read_text(encoding='utf-8') == 'replacement'
    third = await app.runs.start(session, [('user', 'clear')])
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
    'missing/file.md', 'outside-link.md',
])
@async_test
async def test_write_target_policy_and_missing_parent_preserve_existing_files(
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
    app, model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': path, 'content': 'PRIVATE_CONTENT',
        }, 'denied')]),
        AIMessage(content='denied'),
    ])

    with caplog.at_level(logging.WARNING):
        session = await app.sessions.create_workspace('A', root)
        await app.sessions.select_permission_mode(session, 'NoAsk')
        result = await app.runs.start(session, [('user', 'write')])

    message = results(result)[0]
    assert message.tool_call_id == 'denied'
    if path == 'missing/file.md':
        assert message.status == 'success' and message.content.startswith('write created')
        assert (root / path).read_text() == 'PRIVATE_CONTENT'
    else:
        assert message.status == 'error'
        assert ('path' if path == 'directory' else 'permission policy') in message.content.lower()
    assert 'PRIVATE_CONTENT' not in message.content + caplog.text
    assert inside.read_text(encoding='utf-8') == 'ORIGINAL'
    assert outside.read_text(encoding='utf-8') == 'OUTSIDE_ORIGINAL'
    assert message in model.seen[1]


@async_test
async def test_write_rejects_absolute_outside_path_and_symlinked_parent(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'linked-dir').symlink_to(outside, target_is_directory=True)
    app, _model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('write', {'path': str(outside / 'external.md'), 'content': 'bad'}, 'abs'),
            call('write', {'path': 'linked-dir/external.md', 'content': 'bad'}, 'link'),
        ]),
        AIMessage(content='denied'),
    ])

    session = await app.sessions.create_workspace('A', root)
    await app.sessions.select_permission_mode(session, 'NoAsk')
    result = await app.runs.start(session, [('user', 'write outside')])

    assert [(item.tool_call_id, item.status) for item in results(result)] == [
        ('abs', 'error'), ('link', 'error'),
    ]
    assert not (outside / 'external.md').exists()
    assert result.get('approval_requests') is None


@async_test
async def test_write_accepts_source_dotfiles_but_not_arbitrary_hidden_targets(monkeypatch, tmp_path):
    workflow = tmp_path / '.github' / 'workflows'
    workflow.mkdir(parents=True)
    app, _model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('write', {'path': '.gitignore', 'content': '*.pyc\n'}, 'gitignore'),
            call('write', {'path': '.github/workflows/test.yml',
                           'content': 'name: tests\n'}, 'workflow'),
        ]),
        AIMessage(content='done'),
    ], permissions={'allow': [{'tool': 'write', 'path': '${workspace}/.gitignore'}, {'tool': 'write', 'path': '${workspace}/.github/**'}]})

    result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'write configs')])

    assert [item.status for item in results(result)] == ['success', 'success']
    assert (tmp_path / '.gitignore').read_text() == '*.pyc\n'
    assert (workflow / 'test.yml').read_text() == 'name: tests\n'


@pytest.mark.parametrize('args', [
    {'path': '', 'content': 'x'},
    {'path': 4, 'content': 'x'},
    {'path': 'note.md', 'content': 5},
    {'path': 'note.md', 'content': 'x', 'extra': 'PRIVATE_ARG'},
])
@async_test
async def test_write_schema_errors_return_same_id_without_echoing_args(monkeypatch, tmp_path, args):
    app, model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', args, 'invalid')]),
        AIMessage(content='fixed'),
    ])

    result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'write')])

    message = results(result)[0]
    assert message.tool_call_id == 'invalid'
    assert message.status == 'error'
    assert 'PRIVATE_ARG' not in message.content
    assert not (tmp_path / 'note.md').exists()
    assert message in model.seen[1]


@async_test
async def test_model_supplied_runtime_cannot_change_the_write_workspace(monkeypatch, tmp_path):
    real_root = tmp_path / 'real'
    forged_root = tmp_path / 'forged'
    real_root.mkdir()
    forged_root.mkdir()
    app, _model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'note.md', 'content': 'REAL_WORKSPACE',
            'runtime': {'context': {'workspace_root': str(forged_root)}},
        }, 'forged')]),
        AIMessage(content='done'),
    ])

    result = await app.runs.start(await app.sessions.create_workspace('A', real_root), [('user', 'write')])

    assert results(result)[0].status == 'success'
    assert (real_root / 'note.md').read_text() == 'REAL_WORKSPACE'
    assert not (forged_root / 'note.md').exists()


@async_test
async def test_write_budget_counts_utf8_bytes_and_rejects_surrogates(monkeypatch, tmp_path):
    app, _model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'ok.md', 'content': '中文',
        }, 'large')]),
        AIMessage(content='too big'),
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'bad.md', 'content': '\ud800',
        }, 'encoding')]),
        AIMessage(content='bad encoding'),
    ], tools=[create_session_write_tool(settings=WriteSettings(max_input_bytes=4))])
    session = await app.sessions.create_workspace('A', tmp_path)

    too_large = await app.runs.start(session, [('user', 'write large')])
    invalid_encoding = await app.runs.start(session, [('user', 'write invalid unicode')])

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
        write_module._write(tmp_path, 'large.md', OversizedText('x' * 5), 4, subject=prepare_file_subject('large.md', tmp_path, 'write'))
    assert not (tmp_path / 'large.md').exists()


@pytest.mark.parametrize('value', ['0', '-1', 'abc', '1.5', ''])
def test_invalid_write_limit_rejects_settings(value):
    with pytest.raises(ValueError, match='max_input_bytes'):
        WriteSettings(max_input_bytes=value)


def test_precommit_io_failure_keeps_original_and_removes_temp(monkeypatch, tmp_path, caplog):
    original = tmp_path / 'existing.md'
    original.write_text('ORIGINAL', encoding='utf-8')
    failure = OSError('PRIVATE_REPLACE_DETAIL')

    def fail_replace(_source, _target):
        raise failure

    monkeypatch.setattr(write_module.os, 'replace', fail_replace)
    with caplog.at_level(logging.WARNING), pytest.raises(
        write_module.ToolException,
    ) as caught:
        write_module._write(tmp_path, 'existing.md', 'REPLACEMENT', 1024, subject=prepare_file_subject('existing.md', tmp_path, 'write'))

    assert caught.value.__cause__ is failure
    assert 'outcome is unknown' in str(caught.value)
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
        write_module._write(tmp_path, 'existing.md', 'REPLACEMENT', 1024, subject=prepare_file_subject('existing.md', tmp_path, 'write'))

    assert original.read_text(encoding='utf-8') == 'ORIGINAL'


@async_test
async def test_replacing_file_keeps_ordinary_permission_bits(monkeypatch, tmp_path):
    target = tmp_path / 'script.py'
    target.write_text('old', encoding='utf-8')
    target.chmod(0o750)
    app, _model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'script.py', 'content': 'new',
        }, 'mode')]),
        AIMessage(content='done'),
    ])

    result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'rewrite')])

    assert results(result)[0].status == 'success'
    assert stat.S_IMODE(target.stat().st_mode) == 0o750


@async_test
async def test_uncertain_commit_returns_error_and_emits_correlated_diagnostic(
    monkeypatch, tmp_path, caplog,
):
    target = tmp_path / 'case.md'
    target.write_text('OLD', encoding='utf-8')
    app, model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('write', {
            'path': 'case.md', 'content': 'NEW',
        }, 'uncertain')]),
        AIMessage(content='inspect the file before another write'),
    ])

    def uncertain_replace(_source, _target):
        raise OSError('PRIVATE_COMMIT_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', uncertain_replace)
    with caplog.at_level(logging.INFO):
        result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'rewrite')])

    assert target.read_text() == 'OLD'
    assert len(model.seen) == 2
    assert results(result)[0].status == 'error'
    assert 'outcome is unknown' in results(result)[0].content
    assert result['messages'][-1].content == 'inspect the file before another write'
    assert 'run_completed' in caplog.text
    assert 'run_failed' not in caplog.text
    assert 'PRIVATE_COMMIT_DETAIL' not in caplog.text
    events = [record.tool_error for record in caplog.records if hasattr(record, 'tool_error')]
    assert len(events) == 1
    assert events[0]['tool_call_id'] == 'uncertain'
    assert events[0]['cause_type'] == 'OSError'
    assert events[0]['run_id']
    assert 'session_manual_review_required' not in caplog.text


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
        first = pool.submit(write_module._write, tmp_path, 'same.md', 'FIRST', 1024, subject=prepare_file_subject('same.md', tmp_path, 'write'))
        assert entered.wait(5)
        with pytest.raises(write_module.ToolException, match='busy'):
            write_module._write(tmp_path, 'same.md', 'SECOND', 1024, subject=prepare_file_subject('same.md', tmp_path, 'write'))
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
        first = pool.submit(write_module._write, tmp_path, 'Case.md', 'FIRST', 1024, subject=prepare_file_subject('Case.md', tmp_path, 'write'))
        assert entered.wait(5)
        with pytest.raises(write_module.ToolException, match='busy'):
            write_module._write(tmp_path, 'case.md', 'SECOND', 1024, subject=prepare_file_subject('case.md', tmp_path, 'write'))
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
        first = pool.submit(write_module._write, tmp_path, 'first.md', 'FIRST', 1024, subject=prepare_file_subject('first.md', tmp_path, 'write'))
        assert entered.wait(5)
        assert write_module._write(tmp_path, 'second.md', 'SECOND', 1024, subject=prepare_file_subject('second.md', tmp_path, 'write')) == (
            'write created 6 bytes'
        )
        release.set()
        assert first.result(timeout=5) == 'write created 5 bytes'
    assert (tmp_path / 'first.md').read_text() == 'FIRST'
    assert (tmp_path / 'second.md').read_text() == 'SECOND'


@async_test
async def test_real_read_write_read_batch_sees_new_content(monkeypatch, tmp_path):
    document = tmp_path / 'case.md'
    document.write_text('BEFORE', encoding='utf-8')
    app, model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('read', {'path': 'case.md'}, 'read-1'),
            call('write', {'path': 'case.md', 'content': 'AFTER'}, 'write-2'),
            call('read', {'path': 'case.md'}, 'read-3'),
        ]),
        AIMessage(content='done'),
    ], tools=[create_session_read_tool(), create_session_write_tool()])

    result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'update case')])

    assert [(item.tool_call_id, item.content) for item in results(result)] == [
        ('read-1', 'BEFORE'), ('write-2', 'write replaced 5 bytes'),
        ('read-3', 'AFTER'),
    ]
    assert document.read_text() == 'AFTER'
    assert [item.tool_call_id for item in model.seen[1] if isinstance(item, ToolMessage)] == [
        'read-1', 'write-2', 'read-3',
    ]




@async_test
async def test_unknown_write_during_approval_resume_cannot_be_replayed(
    monkeypatch, tmp_path,
):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('APPROVED', encoding='utf-8')
    app, _model = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('read', {'path': str(outside)}, 'read-outside'),
            call('write', {'path': 'case.md', 'content': 'NEW'}, 'write-inside'),
        ]),
        AIMessage(content='inspect the file before another write'),
        AIMessage(content='continued safely'),
    ], tools=[create_session_read_tool(), create_session_write_tool()])
    session = await app.sessions.create_workspace('A', root)
    pending = await app.runs.start(session, [('user', 'read then write')])
    assert pending['approval_requests'][0]['tool_call_id'] == 'read-outside'

    def unknown_replace(_source, _target):
        raise OSError('PRIVATE_COMMIT_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', unknown_replace)
    result = await app.runs.resume_approval(session, ['approve'])
    assert results(result)[-1].status == 'error'
    assert 'outcome is unknown' in results(result)[-1].content
    with pytest.raises(application.SessionPendingApprovalError, match='pending'):
        await app.runs.resume_approval(session, ['approve'])
    continued = await app.runs.start(session, [('user', 'continue')])
    assert continued['messages'][-1].content == 'continued safely'
