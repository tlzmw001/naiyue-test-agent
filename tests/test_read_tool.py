"""Read-file tool contract before it is exposed to a model."""

from native_application_helpers import async_test

import logging
from pathlib import Path

import pytest
from tool_feedback_helpers import EXPECTED_NOASK, invoke_tool_feedback
from langchain_core.tools import ToolException
from langchain.tools import ToolRuntime
from langgraph.errors import GraphInterrupt

from naiyue_test_agent.tools.read import create_session_read_tool, resolve_workspace_root


EXPECTED_UNAVAILABLE = ('read cannot access this path: it may be missing, not a regular '
                        'file, or blocked by workspace policy. Check the path or choose an '
                        'allowed file.')
EXPECTED_OUTSIDE = ('read cannot read this file outside the workspace without a valid '
                    'approval. Request approval for the current target or choose a file '
                    'inside the workspace.')
EXPECTED_IO_FAILURE = ('read could not complete the file read due to an I/O error. '
                       'Check file availability or choose another file.')
EXPECTED_ARGS = ('Invalid read arguments. Provide path as a non-empty string; '
                 'offset as an integer >= 1 and limit as an integer from 1 to 2000 '
                 'when present; remove unknown fields.')
EXPECTED_ENCODING = ('read only supports UTF-8 text; this file is not valid UTF-8. '
                     'Choose a UTF-8 text file.')
EXPECTED_NON_TEXT = ('read only supports text; this file contains non-text control '
                     'bytes. Choose a text file.')


async def call_read(tool, args, root):
    return (await invoke_tool_feedback(tool, args, context={'workspace_root': root}, call_id='call-1'))


@async_test
async def test_read_accepts_relative_and_absolute_paths_within_workspace(tmp_path):
    document = tmp_path / 'api' / 'contract.md'
    document.parent.mkdir()
    document.write_text('折扣接口契约\n', encoding='utf-8')
    tool = create_session_read_tool()

    assert tool.tool.name == 'read'
    assert set(tool.tool.tool_call_schema.model_fields) == {'path', 'offset', 'limit'}
    for path in ('api/contract.md', str(document)):
        result = (await call_read(tool, {'path': path}, tmp_path))
        assert result.status == 'success'
        assert result.content == '折扣接口契约\n'


@async_test
@pytest.mark.parametrize('args', [
    {'path': ''}, {'path': 3}, {'path': 'contract.md', 'extra': 'PRIVATE_ARG'},
])
async def test_bad_schema_returns_safe_error_without_echoing_args(tmp_path, args):
    tool = create_session_read_tool()

    result = (await call_read(tool, args, tmp_path))

    assert result.status == 'error'
    assert result.content == EXPECTED_ARGS
    assert 'PRIVATE_ARG' not in str(result)


@async_test
@pytest.mark.parametrize('path', [
    '../outside.md', '.env', '.env.local', '.hidden/contract.md',
    'key.pem', 'private.key', 'directory', 'outside-link.md',
])
async def test_read_rejects_traversal_sensitive_and_nonfile_targets(tmp_path, path, caplog):
    (tmp_path / '.env').write_text('PRIVATE_ENV', encoding='utf-8')
    (tmp_path / '.env.local').write_text('PRIVATE_LOCAL', encoding='utf-8')
    hidden = tmp_path / '.hidden'
    hidden.mkdir()
    (hidden / 'contract.md').write_text('PRIVATE_HIDDEN', encoding='utf-8')
    (tmp_path / 'key.pem').write_text('PRIVATE_PEM', encoding='utf-8')
    (tmp_path / 'private.key').write_text('PRIVATE_KEY', encoding='utf-8')
    (tmp_path / 'directory').mkdir()
    outside = tmp_path.parent / f'{tmp_path.name}-outside.md'
    outside.write_text('PRIVATE_OUTSIDE', encoding='utf-8')
    (tmp_path / 'outside-link.md').symlink_to(outside)
    tool = create_session_read_tool()

    with caplog.at_level(logging.WARNING):
        result = (await call_read(tool, {'path': path}, tmp_path))

    assert result.status == 'error'
    assert result.content == (EXPECTED_UNAVAILABLE if path == 'directory' else EXPECTED_NOASK)
    assert path not in caplog.text
    assert 'PRIVATE_' not in str(result) + caplog.text



@async_test
async def test_read_rejects_absolute_outside_and_alias_to_sensitive_file(tmp_path):
    outside = tmp_path.parent / f'{tmp_path.name}-outside.md'
    outside.write_text('PRIVATE_OUTSIDE', encoding='utf-8')
    (tmp_path / '.env').write_text('PRIVATE_ENV', encoding='utf-8')
    (tmp_path / 'innocent.md').symlink_to(tmp_path / '.env')
    tool = create_session_read_tool()

    for path in (str(outside), 'innocent.md'):
        result = (await call_read(tool, {'path': path}, tmp_path))
        assert result.status == 'error'
        assert result.content == EXPECTED_NOASK


@async_test
async def test_read_rejects_hidden_symlink_alias(tmp_path):
    document = tmp_path / 'contract.md'
    document.write_text('PRIVATE_VISIBLE', encoding='utf-8')
    (tmp_path / '.hidden.md').symlink_to(document)
    tool = create_session_read_tool()

    result = (await call_read(tool, {'path': '.hidden.md'}, tmp_path))

    assert result.status == 'error'
    assert result.content == EXPECTED_NOASK


@async_test
async def test_read_rejects_missing_and_binary_files(tmp_path):
    (tmp_path / 'binary.dat').write_bytes(b'\xff')
    tool = create_session_read_tool()

    missing = (await call_read(tool, {'path': 'missing.md'}, tmp_path))
    binary = (await call_read(tool, {'path': 'binary.dat'}, tmp_path))
    assert missing.status == binary.status == 'error'
    assert missing.content == EXPECTED_UNAVAILABLE
    assert binary.content == EXPECTED_ENCODING


@async_test
async def test_read_reports_invalid_encoding_and_nontext_without_leaking_content(tmp_path, caplog):
    (tmp_path / 'invalid.md').write_bytes(b'PRIVATE\xffDATA')
    (tmp_path / 'nul.md').write_bytes(b'PRIVATE\x00DATA')
    (tmp_path / 'escape.md').write_bytes(b'PRIVATE\x1bDATA')
    (tmp_path / 'normal.md').write_bytes(b'one\tcolumn\r\ntwo\n')
    tool = create_session_read_tool()

    with caplog.at_level(logging.WARNING):
        invalid = (await call_read(tool, {'path': 'invalid.md'}, tmp_path))
        nul = (await call_read(tool, {'path': 'nul.md'}, tmp_path))
        escape = (await call_read(tool, {'path': 'escape.md'}, tmp_path))
    normal = (await call_read(tool, {'path': 'normal.md'}, tmp_path))

    assert invalid.status == nul.status == escape.status == 'error'
    assert invalid.content == EXPECTED_ENCODING
    assert nul.content == escape.content == EXPECTED_NON_TEXT
    assert normal.status == 'success'
    assert normal.content == 'one\tcolumn\r\ntwo\n'
    assert 'PRIVATE' not in str(invalid) + str(nul) + str(escape) + caplog.text


@async_test
async def test_read_allows_normal_hidden_source_files_without_opening_sensitive_paths(tmp_path):
    (tmp_path / '.gitignore').write_text('*.pyc\n', encoding='utf-8')
    workflow = tmp_path / '.github' / 'workflows' / 'test.yml'
    workflow.parent.mkdir(parents=True)
    workflow.write_text('name: test\n', encoding='utf-8')
    (tmp_path / '.github' / '.env').write_text('PRIVATE_ENV', encoding='utf-8')
    (tmp_path / '.github' / 'private.key').write_text('PRIVATE_KEY', encoding='utf-8')
    (tmp_path / '.hidden.md').write_text('PRIVATE_HIDDEN', encoding='utf-8')
    (tmp_path / '.github' / 'alias.md').symlink_to(tmp_path / '.github' / '.env')
    tool = create_session_read_tool()

    assert (await call_read(tool, {'path': '.gitignore'}, tmp_path)).content == EXPECTED_NOASK
    assert (await call_read(tool, {'path': '.github/workflows/test.yml'}, tmp_path)).content == EXPECTED_NOASK
    for path in ('.github/.env', '.github/private.key', '.hidden.md',
                 '.github/alias.md'):
        result = (await call_read(tool, {'path': path}, tmp_path))
        assert result.status == 'error'
        assert result.content == EXPECTED_NOASK


def test_read_leaves_execution_conversion_to_runtime(tmp_path):
    tool = create_session_read_tool()

    assert tool.tool.handle_tool_error is False
    assert tool.tool.handle_validation_error == EXPECTED_ARGS


@async_test
async def test_read_returns_bounded_prefix_with_explicit_partial_line_notice(tmp_path):
    (tmp_path / 'long.md').write_bytes(b'PRIVATE_' + b'x' * (64 * 1024))
    tool = create_session_read_tool()

    result = (await call_read(tool, {'path': 'long.md'}, tmp_path))

    assert result.status == 'success'
    assert result.content == ('PRIVATE_' + 'x' * (65536 - len('PRIVATE_')) + '\n'
        '[read: line 1 partial; returned_bytes=65536; byte_limit=65536; next_offset=unavailable; '
        'line remainder omitted. offset/limit cannot resume within this line; use a shell to inspect the remainder.]')


@async_test
async def test_read_validates_the_selected_page_as_text(tmp_path):
    (tmp_path / 'paged.md').write_bytes(b'first\nPRIVATE\x00SECOND\n')
    tool = create_session_read_tool()

    first = (await call_read(tool, {'path': 'paged.md', 'offset': 1, 'limit': 1}, tmp_path))
    second = (await call_read(tool, {'path': 'paged.md', 'offset': 2, 'limit': 1}, tmp_path))

    assert first.status == 'success'
    assert first.content == 'first\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]'
    assert second.status == 'error'
    assert second.content == EXPECTED_NON_TEXT
    assert 'PRIVATE' not in str(second)


def test_invalid_workspace_is_rejected_before_tool_registration(tmp_path):
    with pytest.raises(ValueError, match='workspace'):
        resolve_workspace_root(tmp_path / 'missing')
    regular_file = tmp_path / 'file.md'
    regular_file.write_text('content', encoding='utf-8')
    with pytest.raises(ValueError, match='workspace'):
        resolve_workspace_root(regular_file)

    hidden_workspace = tmp_path / '.env'
    hidden_workspace.mkdir()
    assert resolve_workspace_root(hidden_workspace) == hidden_workspace.resolve()
    nested_workspace = hidden_workspace / 'nested'
    nested_workspace.mkdir()
    assert resolve_workspace_root(nested_workspace) == nested_workspace.resolve()


@async_test
async def test_read_io_failure_logs_only_error_type(tmp_path, monkeypatch, caplog):
    document = tmp_path / 'contract.md'
    document.write_text('PRIVATE_CONTENT', encoding='utf-8')
    tool = create_session_read_tool()
    original_open = Path.open

    def fail_open(path, *args, **kwargs):
        if path == document:
            raise OSError('PRIVATE_IO_DETAIL')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_open)
    with caplog.at_level(logging.WARNING):
        result = (await call_read(tool, {'path': 'contract.md'}, tmp_path))

    assert result.status == 'error'
    assert result.content == EXPECTED_IO_FAILURE
    assert 'OSError' in caplog.text
    assert 'PRIVATE_' not in caplog.text + str(result)


@async_test
async def test_read_pages_large_text_without_rejecting_the_whole_file(tmp_path):
    (tmp_path / 'large.md').write_text('entry\n' * 15000, encoding='utf-8')
    tool = create_session_read_tool()

    first = (await call_read(tool, {'path': 'large.md'}, tmp_path))

    assert first.status == 'success'
    assert first.content.startswith('entry\n')
    assert '[read: lines 1-2000; next_offset=2001]\n[read: stopped by 2000 line page limit; continue with the same path, offset=2001, limit=2000]' in first.content
    assert len(first.content.split('[read:', 1)[0].encode('utf-8')) <= 64 * 1024
    second = (await call_read(tool, {'path': 'large.md', 'offset': 2001, 'limit': 2}, tmp_path))
    assert second.status == 'success'
    assert second.content == 'entry\nentry\n[read: lines 2001-2002; next_offset=2003]\n[read: stopped by 2 line page limit; continue with the same path, offset=2003, limit=2]'


@async_test
async def test_read_enforces_line_limit_even_below_byte_budget(tmp_path):
    (tmp_path / 'many-short-lines.md').write_text('x\n' * 2001, encoding='utf-8')
    tool = create_session_read_tool()

    first = (await call_read(tool, {'path': 'many-short-lines.md'}, tmp_path))

    assert first.status == 'success'
    assert first.content == 'x\n' * 2000 + (
        '[read: lines 1-2000; next_offset=2001]\n[read: stopped by 2000 line page limit; continue with the same path, offset=2001, limit=2000]'
    )
    assert (await call_read(tool, {'path': 'many-short-lines.md', 'offset': 2001}, tmp_path)).content == (
        'x\n[read: lines 2001-2001; next_offset=none]'
    )


@async_test
@pytest.mark.parametrize('args', [
    {'offset': 0}, {'offset': -1}, {'offset': '2'}, {'offset': True},
    {'limit': 0}, {'limit': -1}, {'limit': '2'}, {'limit': True},
    {'limit': 2001},
])
async def test_read_rejects_invalid_pagination_parameters(tmp_path, args):
    (tmp_path / 'doc.md').write_text('one\ntwo\n', encoding='utf-8')
    tool = create_session_read_tool()

    result = (await call_read(tool, {'path': 'doc.md', **args}, tmp_path))

    assert result.status == 'error'
    assert result.content == EXPECTED_ARGS


@async_test
async def test_read_pages_utf8_crlf_and_reports_end_of_file(tmp_path):
    (tmp_path / 'doc.md').write_bytes('折扣\r\n第二行\r\n'.encode('utf-8'))
    tool = create_session_read_tool()

    first = (await call_read(tool, {'path': 'doc.md', 'offset': 1, 'limit': 1}, tmp_path))
    second = (await call_read(tool, {'path': 'doc.md', 'offset': 2, 'limit': 1}, tmp_path))
    past_end = (await call_read(tool, {'path': 'doc.md', 'offset': 3}, tmp_path))

    assert first.content == '折扣\r\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]'
    assert second.content == '第二行\r\n[read: lines 2-2; next_offset=none]'
    assert past_end.content == '[read: no lines from offset=3; next_offset=none]'


@async_test
async def test_read_empty_and_overlong_line_are_bounded(tmp_path):
    (tmp_path / 'empty.md').write_text('', encoding='utf-8')
    (tmp_path / 'long.md').write_text('x' * (64 * 1024 + 1), encoding='utf-8')
    tool = create_session_read_tool()

    assert (await call_read(tool, {'path': 'empty.md'}, tmp_path)).content == ''
    assert (await call_read(tool, {'path': 'empty.md', 'offset': 1}, tmp_path)).content == (
        '[read: no lines from offset=1; next_offset=none]'
    )
    result = (await call_read(tool, {'path': 'long.md'}, tmp_path))
    assert result.status == 'success'
    assert result.content == ('x' * 65536 + '\n'
        '[read: line 1 partial; returned_bytes=65536; byte_limit=65536; next_offset=unavailable; '
        'line remainder omitted. offset/limit cannot resume within this line; use a shell to inspect the remainder.]')


@async_test
async def test_read_uses_full_body_budget_even_with_continuation_note(tmp_path):
    body = 'x' * (64 * 1024)
    (tmp_path / 'exact.md').write_text(body, encoding='utf-8')
    tool = create_session_read_tool()

    result = (await call_read(tool, {'path': 'exact.md', 'offset': 1, 'limit': 1}, tmp_path))

    assert result.status == 'success'
    assert result.content == body + '\n[read: lines 1-1; next_offset=none]'
    assert len(result.content.encode('utf-8')) > 64 * 1024


@async_test
async def test_read_page_budget_and_file_change_between_calls(tmp_path):
    document = tmp_path / 'doc.md'
    document.write_text(('é' * 180 + '\n') * 400, encoding='utf-8')
    tool = create_session_read_tool()

    first = (await call_read(tool, {'path': 'doc.md'}, tmp_path))
    assert first.status == 'success'
    assert len(first.content.split('[read:', 1)[0].encode('utf-8')) <= 64 * 1024
    assert 'next_offset=none' not in first.content

    document.write_text('changed\n', encoding='utf-8')
    second = (await call_read(tool, {'path': 'doc.md', 'offset': 1, 'limit': 1}, tmp_path))
    assert second.content == 'changed\n[read: lines 1-1; next_offset=none]'


@async_test
async def test_read_errors_explain_cause_and_next_action_without_echoing_input(tmp_path, monkeypatch):
    (tmp_path / 'bad-encoding.md').write_bytes(b'PRIVATE\xff')
    (tmp_path / 'control.md').write_bytes(b'PRIVATE\x00')
    (tmp_path / 'io.md').write_text('PRIVATE', encoding='utf-8')
    outside = tmp_path.parent / f'{tmp_path.name}-outside.md'
    outside.write_text('PRIVATE', encoding='utf-8')
    tool = create_session_read_tool()

    original_open = Path.open

    def fail_open(path, *args, **kwargs):
        if path == tmp_path / 'io.md':
            raise OSError('PRIVATE_IO_DETAIL')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_open)
    cases = [
        ({'path': ''}, ('path', 'offset', 'limit', 'remove unknown fields')),
        ({'path': 'missing.md'}, ('missing', 'workspace policy', 'Check the path')),
        ({'path': str(outside)}, ('not executed', 'permission policy', 'NoAsk')),
        ({'path': 'bad-encoding.md'}, ('UTF-8', 'Choose')),
        ({'path': 'control.md'}, ('non-text', 'Choose')),
        ({'path': 'io.md'}, ('I/O', 'Check')),
    ]
    for args, expected in cases:
        result = (await call_read(tool, args, tmp_path))
        assert result.status == 'error'
        for detail in expected:
            assert detail in result.content
        assert 'PRIVATE' not in str(result)


@async_test
async def test_read_explicit_public_diagnosis_does_not_need_whitelist(tmp_path, monkeypatch):
    from naiyue_test_agent.tools import read as read_module
    tool = create_session_read_tool()
    (tmp_path / 'doc.md').write_text('fixture')

    def diagnose(*_args, **_kwargs):
        raise ToolException('Choose a supported input for this operation.')

    monkeypatch.setattr(read_module, '_read', diagnose)
    message = (await call_read(tool, {'path': 'doc.md'}, tmp_path))
    assert message.status == 'error'
    assert message.content == 'Choose a supported input for this operation.'


@async_test
async def test_unexpected_read_only_failure_returns_safe_tool_message(tmp_path, monkeypatch, caplog):
    document = tmp_path / 'doc.md'
    document.write_text('PRIVATE_CONTENT', encoding='utf-8')
    tool = create_session_read_tool()
    original_open = Path.open

    def fail_open(path, *args, **kwargs):
        if path == document:
            raise LookupError('PRIVATE_INTERNAL_DETAIL')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_open)
    with caplog.at_level(logging.WARNING):
        result = (await call_read(tool, {'path': 'doc.md'}, tmp_path))

    assert result.status == 'error'
    assert 'Tool execution failed (LookupError)' in result.content
    assert 'decide the next step' in result.content
    assert 'LookupError' in caplog.text
    assert 'PRIVATE' not in str(result) + caplog.text


def test_read_does_not_turn_graph_interrupt_into_tool_error(tmp_path, monkeypatch):
    document = tmp_path / 'doc.md'
    document.write_text('content', encoding='utf-8')
    tool = create_session_read_tool()
    original_open = Path.open

    def interrupt_open(path, *args, **kwargs):
        if path == document:
            raise GraphInterrupt()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', interrupt_open)
    from naiyue_test_agent.tools.permissions import execution_environment
    subject = tool.permissions.prepare({'path': 'doc.md'}, {'workspace_root': tmp_path})
    subject.update(args={'path': 'doc.md'}, path_field='path', execution_environment=execution_environment())
    permit = {'call_id': 'call-1', 'session_id': 's', 'agent_path': 'root', 'subject': subject}
    runtime = ToolRuntime(state={}, context={'workspace_root': tmp_path, 'session_id': 's', '_execution_permit': permit}, config={},
                          stream_writer=lambda _: None, tool_call_id='call-1', store=None)
    with pytest.raises(GraphInterrupt):
        tool.tool.invoke({'name': 'read', 'args': {'path': 'doc.md', 'runtime': runtime},
                     'id': 'call-1', 'type': 'tool_call'})
