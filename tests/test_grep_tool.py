"""Grep searches only readable text and returns matched lines to the model."""

from native_application_helpers import async_test

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from tool_feedback_helpers import invoke_tool_feedback

from naiyue_test_agent.tools import grep as grep_module
from naiyue_test_agent.tools.grep import create_session_grep_tool


async def call_grep(tool, args, root):
    return (await invoke_tool_feedback(tool, args, context={'workspace_root': root}, call_id='grep-call'))


@async_test
async def test_grep_regex_literal_case_context_and_glob(tmp_path):
    (tmp_path / 'api.md').write_text('before\nError 42\nafter\n', encoding='utf-8')
    (tmp_path / 'other.py').write_text('Error 43\n', encoding='utf-8')
    tool = create_session_grep_tool()
    assert set(tool.tool.tool_call_schema.model_fields) == {
        'pattern', 'path', 'glob', 'ignoreCase', 'literal', 'context', 'limit', 'timeout',
    }

    regex = (await call_grep(tool, {'pattern': r'Error \d+', 'glob': '*.md'}, tmp_path))
    assert regex.status == 'success'
    assert regex.content == 'api.md:2: Error 42'
    assert (await call_grep(tool, {'pattern': 'error 42', 'ignoreCase': True,
                            'glob': '*.md'}, tmp_path)).content == 'api.md:2: Error 42'
    assert (await call_grep(tool, {'pattern': r'Error \d+', 'literal': True}, tmp_path)).content == (
        'No matches found'
    )
    context = (await call_grep(tool, {'pattern': 'Error 42', 'context': 1,
                               'path': 'api.md'}, tmp_path))
    assert context.content == 'api.md-1- before\napi.md:2: Error 42\napi.md-3- after'


@async_test
async def test_grep_respects_gitignore_and_read_policy(tmp_path):
    (tmp_path / '.gitignore').write_text('ignored.md\n', encoding='utf-8')
    (tmp_path / 'ignored.md').write_text('NEEDLE_IGNORED', encoding='utf-8')
    (tmp_path / '.env').write_text('NEEDLE_SECRET', encoding='utf-8')
    (tmp_path / 'private.key').write_text('NEEDLE_KEY', encoding='utf-8')
    (tmp_path / 'visible.md').write_text('NEEDLE_VISIBLE', encoding='utf-8')
    (tmp_path / '.github').mkdir()
    (tmp_path / '.github' / 'workflow.yml').write_text('NEEDLE_WORKFLOW')
    tool = create_session_grep_tool()

    result = (await call_grep(tool, {'pattern': 'NEEDLE_'}, tmp_path))
    assert result.status == 'success'
    assert 'visible.md:1: NEEDLE_VISIBLE' in result.content
    assert 'NEEDLE_WORKFLOW' not in result.content
    assert '.github/workflow.yml: unapproved_private_or_external' in result.content
    assert 'NEEDLE_SECRET' not in result.content
    assert 'NEEDLE_KEY' not in result.content
    assert 'NEEDLE_IGNORED' not in result.content
    for path in ('.env', 'private.key'):
        denied = (await call_grep(tool, {'pattern': 'NEEDLE_', 'path': path}, tmp_path))
        assert denied.status == 'error'
        assert 'NEEDLE_' not in denied.content


@async_test
async def test_grep_match_limit_empty_and_invalid_regex(tmp_path):
    (tmp_path / 'many.md').write_text('hit\nhit\nhit\n', encoding='utf-8')
    tool = create_session_grep_tool()
    limited = (await call_grep(tool, {'pattern': 'hit', 'limit': 2}, tmp_path))
    assert limited.status == 'success'
    assert limited.content.startswith('many.md:1: hit\nmany.md:2: hit\n')
    assert 'truncated' in limited.content.lower()
    assert 'many.md:3:' not in limited.content
    assert (await call_grep(tool, {'pattern': 'absent'}, tmp_path)).content == 'No matches found'
    invalid = (await call_grep(tool, {'pattern': '('}, tmp_path))
    assert invalid.status == 'error'
    assert 'regex' in invalid.content.lower()
    assert 'literal' in invalid.content.lower()


@async_test
async def test_grep_invalid_regex_is_reported_even_without_candidate_files(tmp_path):
    invalid = (await call_grep(create_session_grep_tool(), {'pattern': '('}, tmp_path))
    assert invalid.status == 'error'
    assert 'regex' in invalid.content.lower()


@async_test
async def test_grep_invalid_glob_has_an_actionable_error(tmp_path):
    invalid = (await call_grep(create_session_grep_tool(), {'pattern': 'x', 'glob': '['}, tmp_path))
    assert invalid.status == 'error'
    assert 'glob' in invalid.content.lower()


@async_test
@pytest.mark.parametrize('args', [
    {}, {'pattern': ''}, {'pattern': '   '}, {'pattern': 8},
    {'pattern': 'x', 'path': ''},
    {'pattern': 'x', 'context': -1}, {'pattern': 'x', 'context': 11},
    {'pattern': 'x', 'limit': 0}, {'pattern': 'x', 'limit': 1001},
    {'pattern': 'x', 'literal': 1}, {'pattern': 'x', 'extra': 'PRIVATE_ARGUMENT'},
])
async def test_grep_schema_errors_are_safe(tmp_path, args):
    result = (await call_grep(create_session_grep_tool(), args, tmp_path))
    assert result.status == 'error'
    assert 'pattern' in result.content and 'limit' in result.content
    assert 'PRIVATE_ARGUMENT' not in str(result)


@async_test
async def test_grep_external_directory_is_not_bulk_approved(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'PRIVATE.md').write_text('PRIVATE_CONTENT')
    result = (await call_grep(create_session_grep_tool(), {
        'pattern': 'PRIVATE', 'path': str(outside),
    }, root))
    assert result.status == 'error'
    assert result.content == 'Tool was not executed: access denied by the permission policy (NoAsk).'
    assert 'PRIVATE_CONTENT' not in str(result)


@async_test
async def test_grep_missing_rg_is_actionable_tool_error(monkeypatch, tmp_path):
    monkeypatch.setattr(grep_module.shutil, 'which', lambda _name: None)
    result = (await call_grep(create_session_grep_tool(), {'pattern': 'x'}, tmp_path))
    assert result.status == 'error'
    assert 'rg' in result.content
    assert 'install' in result.content.lower()


@async_test
async def test_grep_uses_rg_encoding_and_binary_reports_with_safe_text(tmp_path, caplog):
    (tmp_path / 'bad.md').write_bytes(b'PRIVATE\xffVALUE')
    (tmp_path / 'nul.md').write_bytes(b'PRIVATE\x00VALUE')
    (tmp_path / 'visible.md').write_text('VISIBLE_VALUE\n')
    tool = create_session_grep_tool()

    invalid = (await call_grep(tool, {'pattern': 'PRIVATE', 'path': 'bad.md'}, tmp_path))
    assert invalid.status == 'success' and 'PRIVATE�VALUE' in invalid.content
    assert 'non-UTF-8' in invalid.content
    binary = (await call_grep(tool, {'pattern': 'PRIVATE', 'path': 'nul.md'}, tmp_path))
    assert binary.status == 'success' and binary.content.startswith('nul.md:1: PRIVATE\n')
    assert 'binary content' in binary.content and 'not searched' in binary.content
    assert '\x00' not in binary.content
    directory = (await call_grep(tool, {'pattern': 'VALUE'}, tmp_path))
    assert directory.status == 'success'
    assert 'visible.md:1: VISIBLE_VALUE' in directory.content
    assert 'binary content' in directory.content and 'non-UTF-8' in directory.content
    assert 'PRIVATE' not in caplog.text


@async_test
async def test_grep_escapes_control_characters_and_bounds_output(tmp_path):
    (tmp_path / 'control.md').write_text('KEY\tVALUE\n', encoding='utf-8')
    tool = create_session_grep_tool()
    escaped = (await call_grep(tool, {'pattern': 'KEY'}, tmp_path))
    assert escaped.content == 'control.md:1: KEY\\tVALUE'

    (tmp_path / 'many.md').write_text(('hit' + 'x' * 997 + '\n') * 60,
                                      encoding='utf-8')
    bounded = (await call_grep(tool, {'pattern': 'hit', 'limit': 100}, tmp_path))
    assert bounded.status == 'success'
    assert len(bounded.content.encode('utf-8')) <= 50 * 1024 + 100
    assert 'truncated' in bounded.content.lower()


@async_test
async def test_grep_leading_dash_pattern_is_data_not_an_rg_option(tmp_path):
    (tmp_path / 'argument.md').write_text('-danger\n', encoding='utf-8')
    result = (await call_grep(create_session_grep_tool(), {'pattern': '-danger',
                                                   'literal': True}, tmp_path))
    assert result.status == 'success'
    assert result.content == 'argument.md:1: -danger'


@async_test
async def test_grep_long_line_is_clipped_without_losing_later_matches(tmp_path):
    (tmp_path / 'long.md').write_text('HIT' + 'x' * 3000 + '\nHIT short\n',
                                      encoding='utf-8')
    result = (await call_grep(create_session_grep_tool(), {'pattern': 'HIT'}, tmp_path))
    assert result.status == 'success'
    assert 'long.md:1:' in result.content
    assert 'long.md:2: HIT short' in result.content
    assert 'read' in result.content


@async_test
async def test_grep_large_explicit_file_is_searched_without_python_open(monkeypatch, tmp_path):
    large = tmp_path / 'large.md'
    with large.open('wb') as source:
        source.write(b'anything\n' * 1048576)
    original_open = Path.open

    def guard_open(path, *args, **kwargs):
        if path == large:
            raise AssertionError('file content should only be read by rg')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guard_open)
    result = (await call_grep(create_session_grep_tool(), {
        'pattern': 'anything', 'path': 'large.md', 'limit': 1,
    }, tmp_path))
    assert result.status == 'success' and 'large.md:1: anything' in result.content
    assert 'truncated' in result.content.lower()
