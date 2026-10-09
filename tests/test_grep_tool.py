"""Grep searches only readable text and returns matched lines to the model."""

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from naiyue_test_agent.agent_runtime.tools import grep as grep_module
from naiyue_test_agent.agent_runtime.tools.grep import create_grep_tool


def call_grep(tool, args):
    result = tool.invoke({'name': 'grep', 'args': args, 'id': 'grep-call',
                          'type': 'tool_call'})
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == 'grep-call'
    return result


def test_grep_regex_literal_case_context_and_glob(tmp_path):
    (tmp_path / 'api.md').write_text('before\nError 42\nafter\n', encoding='utf-8')
    (tmp_path / 'other.py').write_text('Error 43\n', encoding='utf-8')
    tool = create_grep_tool(tmp_path)
    assert set(tool.tool_call_schema.model_fields) == {
        'pattern', 'path', 'glob', 'ignoreCase', 'literal', 'context', 'limit',
    }

    regex = call_grep(tool, {'pattern': r'Error \d+', 'glob': '*.md'})
    assert regex.status == 'success'
    assert regex.content == 'api.md:2: Error 42'
    assert call_grep(tool, {'pattern': 'error 42', 'ignoreCase': True,
                            'glob': '*.md'}).content == 'api.md:2: Error 42'
    assert call_grep(tool, {'pattern': r'Error \d+', 'literal': True}).content == (
        'No matches found'
    )
    context = call_grep(tool, {'pattern': 'Error 42', 'context': 1,
                               'path': 'api.md'})
    assert context.content == 'api.md-1- before\napi.md:2: Error 42\napi.md-3- after'


def test_grep_respects_gitignore_and_read_policy(tmp_path):
    (tmp_path / '.gitignore').write_text('ignored.md\n', encoding='utf-8')
    (tmp_path / 'ignored.md').write_text('NEEDLE_IGNORED', encoding='utf-8')
    (tmp_path / '.env').write_text('NEEDLE_SECRET', encoding='utf-8')
    (tmp_path / 'private.key').write_text('NEEDLE_KEY', encoding='utf-8')
    (tmp_path / 'visible.md').write_text('NEEDLE_VISIBLE', encoding='utf-8')
    (tmp_path / '.github').mkdir()
    (tmp_path / '.github' / 'workflow.yml').write_text('NEEDLE_WORKFLOW')
    tool = create_grep_tool(tmp_path)

    result = call_grep(tool, {'pattern': 'NEEDLE_'})
    assert result.status == 'success'
    assert 'visible.md:1: NEEDLE_VISIBLE' in result.content
    assert '.github/workflow.yml:1: NEEDLE_WORKFLOW' in result.content
    assert 'NEEDLE_SECRET' not in result.content
    assert 'NEEDLE_KEY' not in result.content
    assert 'NEEDLE_IGNORED' not in result.content
    for path in ('.env', 'private.key'):
        denied = call_grep(tool, {'pattern': 'NEEDLE_', 'path': path})
        assert denied.status == 'error'
        assert 'NEEDLE_' not in denied.content


def test_grep_match_limit_empty_and_invalid_regex(tmp_path):
    (tmp_path / 'many.md').write_text('hit\nhit\nhit\n', encoding='utf-8')
    tool = create_grep_tool(tmp_path)
    limited = call_grep(tool, {'pattern': 'hit', 'limit': 2})
    assert limited.status == 'success'
    assert limited.content.startswith('many.md:1: hit\nmany.md:2: hit\n')
    assert 'truncated' in limited.content.lower()
    assert 'many.md:3:' not in limited.content
    assert call_grep(tool, {'pattern': 'absent'}).content == 'No matches found'
    invalid = call_grep(tool, {'pattern': '('})
    assert invalid.status == 'error'
    assert 'regex' in invalid.content.lower()
    assert 'literal' in invalid.content.lower()


def test_grep_invalid_regex_is_reported_even_without_candidate_files(tmp_path):
    invalid = call_grep(create_grep_tool(tmp_path), {'pattern': '('})
    assert invalid.status == 'error'
    assert 'regex' in invalid.content.lower()


def test_grep_invalid_glob_has_an_actionable_error(tmp_path):
    invalid = call_grep(create_grep_tool(tmp_path), {'pattern': 'x', 'glob': '['})
    assert invalid.status == 'error'
    assert 'glob' in invalid.content.lower()


@pytest.mark.parametrize('args', [
    {}, {'pattern': ''}, {'pattern': '   '}, {'pattern': 8},
    {'pattern': 'x', 'path': ''},
    {'pattern': 'x', 'context': -1}, {'pattern': 'x', 'context': 11},
    {'pattern': 'x', 'limit': 0}, {'pattern': 'x', 'limit': 1001},
    {'pattern': 'x', 'literal': 1}, {'pattern': 'x', 'extra': 'PRIVATE_ARGUMENT'},
])
def test_grep_schema_errors_are_safe(tmp_path, args):
    result = call_grep(create_grep_tool(tmp_path), args)
    assert result.status == 'error'
    assert 'pattern' in result.content and 'limit' in result.content
    assert 'PRIVATE_ARGUMENT' not in str(result)


def test_grep_external_directory_is_not_bulk_approved(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'PRIVATE.md').write_text('PRIVATE_CONTENT')
    result = call_grep(create_grep_tool(root), {
        'pattern': 'PRIVATE', 'path': str(outside),
    })
    assert result.status == 'error'
    assert 'single file' in result.content.lower()
    assert 'PRIVATE_CONTENT' not in str(result)


def test_grep_missing_rg_is_actionable_tool_error(monkeypatch, tmp_path):
    monkeypatch.setattr(grep_module.shutil, 'which', lambda _name: None)
    result = call_grep(create_grep_tool(tmp_path), {'pattern': 'x'})
    assert result.status == 'error'
    assert 'rg' in result.content
    assert 'install' in result.content.lower()


def test_grep_rejects_invalid_utf8_and_nontext_controls(tmp_path):
    (tmp_path / 'bad.md').write_bytes(b'PRIVATE\xffVALUE')
    (tmp_path / 'nul.md').write_bytes(b'PRIVATE\x00VALUE')
    (tmp_path / 'visible.md').write_text('VISIBLE_VALUE\n')
    tool = create_grep_tool(tmp_path)

    for path in ('bad.md', 'nul.md'):
        result = call_grep(tool, {'pattern': 'PRIVATE', 'path': path})
        assert result.status == 'error'
        assert 'UTF-8' in result.content
        assert 'PRIVATE' not in str(result)
    directory = call_grep(tool, {'pattern': 'VALUE'})
    assert directory.status == 'success'
    assert 'visible.md:1: VISIBLE_VALUE' in directory.content
    assert 'skipped' in directory.content.lower()
    assert 'PRIVATE' not in directory.content


def test_grep_escapes_control_characters_and_bounds_output(tmp_path):
    (tmp_path / 'control.md').write_text('KEY\tVALUE\n', encoding='utf-8')
    tool = create_grep_tool(tmp_path)
    escaped = call_grep(tool, {'pattern': 'KEY'})
    assert escaped.content == 'control.md:1: KEY\\tVALUE'

    (tmp_path / 'many.md').write_text(('hit' + 'x' * 997 + '\n') * 60,
                                      encoding='utf-8')
    bounded = call_grep(tool, {'pattern': 'hit', 'limit': 100})
    assert bounded.status == 'success'
    assert len(bounded.content.encode('utf-8')) <= 50 * 1024 + 100
    assert 'truncated' in bounded.content.lower()


def test_grep_leading_dash_pattern_is_data_not_an_rg_option(tmp_path):
    (tmp_path / 'argument.md').write_text('-danger\n', encoding='utf-8')
    result = call_grep(create_grep_tool(tmp_path), {'pattern': '-danger',
                                                   'literal': True})
    assert result.status == 'success'
    assert result.content == 'argument.md:1: -danger'


def test_grep_long_line_is_clipped_without_losing_later_matches(tmp_path):
    (tmp_path / 'long.md').write_text('HIT' + 'x' * 3000 + '\nHIT short\n',
                                      encoding='utf-8')
    result = call_grep(create_grep_tool(tmp_path), {'pattern': 'HIT'})
    assert result.status == 'success'
    assert 'long.md:1:' in result.content
    assert 'long.md:2: HIT short' in result.content
    assert 'read' in result.content


def test_grep_oversized_explicit_file_is_rejected_before_open(monkeypatch, tmp_path):
    large = tmp_path / 'large.md'
    with large.open('wb') as source:
        source.truncate(grep_module.MAX_FILE_BYTES + 1)
    original_open = Path.open

    def guard_open(path, *args, **kwargs):
        if path == large:
            raise AssertionError('oversized file should not be opened')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guard_open)
    result = call_grep(create_grep_tool(tmp_path), {
        'pattern': 'anything', 'path': 'large.md',
    })
    assert result.status == 'error'
    assert 'budget' in result.content.lower()
