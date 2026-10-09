"""Find returns bounded names while respecting the search workspace."""

import pytest
from langchain_core.messages import ToolMessage

from naiyue_test_agent.agent_runtime.tools import find as find_module
from naiyue_test_agent.agent_runtime.tools.find import create_find_tool


def call_find(tool, args):
    result = tool.invoke({'name': 'find', 'args': args, 'id': 'find-call',
                          'type': 'tool_call'})
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == 'find-call'
    return result


def test_find_glob_paths_hidden_names_and_gitignore(tmp_path):
    (tmp_path / '.gitignore').write_text('ignored.md\n')
    (tmp_path / 'ignored.md').write_text('ignored')
    (tmp_path / 'root.md').write_text('root')
    (tmp_path / '.hidden.md').write_text('hidden')
    source = tmp_path / 'src'
    source.mkdir()
    (source / 'inner.md').write_text('inner')
    (source / 'code.py').write_text('code')
    nested = source / 'nested' / 'deep'
    nested.mkdir(parents=True)
    (nested / 'more.py').write_text('deep')
    (tmp_path / 'root.py').write_text('root code')
    tool = create_find_tool(tmp_path)

    assert set(tool.tool_call_schema.model_fields) == {'pattern', 'path', 'limit'}
    result = call_find(tool, {'pattern': '*.md'})
    assert result.status == 'success'
    assert result.content == '.hidden.md\nroot.md\nsrc/inner.md'
    assert call_find(tool, {'pattern': '*.md', 'path': 'src'}).content == 'inner.md'
    assert call_find(tool, {'pattern': 'src/**/*.py'}).content == (
        'src/code.py\nsrc/nested/deep/more.py'
    )
    assert call_find(tool, {'pattern': '**/*.py'}).content == (
        'root.py\nsrc/code.py\nsrc/nested/deep/more.py'
    )


def test_find_empty_and_result_limit(tmp_path):
    for name in ('a.py', 'b.py', 'c.py'):
        (tmp_path / name).write_text(name)
    tool = create_find_tool(tmp_path)
    assert call_find(tool, {'pattern': '*.txt'}).content == (
        'No files found matching pattern'
    )
    limited = call_find(tool, {'pattern': '*.py', 'limit': 2})
    assert limited.status == 'success'
    assert limited.content.startswith('a.py\nb.py\n')
    assert 'truncated' in limited.content.lower()
    assert 'c.py' not in limited.content


@pytest.mark.parametrize('args', [
    {}, {'pattern': ''}, {'pattern': 3}, {'pattern': '*.py', 'path': ''},
    {'pattern': '*.py', 'limit': 0}, {'pattern': '*.py', 'limit': 2001},
    {'pattern': '*.py', 'extra': 'PRIVATE_ARGUMENT'},
])
def test_find_schema_errors_are_safe(tmp_path, args):
    result = call_find(create_find_tool(tmp_path), args)
    assert result.status == 'error'
    assert 'pattern' in result.content and 'limit' in result.content
    assert 'PRIVATE_ARGUMENT' not in str(result)


def test_find_rejects_traversal_non_directory_and_outside_without_grant(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'ordinary.txt').write_text('x')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'PRIVATE.md').write_text('x')
    tool = create_find_tool(root)
    for path in ('../outside', 'ordinary.txt', 'missing'):
        result = call_find(tool, {'pattern': '*', 'path': path})
        assert result.status == 'error'
        assert 'PRIVATE.md' not in str(result)
    external = call_find(tool, {'pattern': '*', 'path': str(outside)})
    assert external.status == 'error'
    assert 'approval' in external.content.lower()


def test_find_missing_rg_is_actionable_tool_error(monkeypatch, tmp_path):
    monkeypatch.setattr(find_module.shutil, 'which', lambda _name: None)
    result = call_find(create_find_tool(tmp_path), {'pattern': '*.md'})
    assert result.status == 'error'
    assert 'rg' in result.content
    assert 'install' in result.content.lower()


def test_find_invalid_glob_and_output_budget(tmp_path):
    tool = create_find_tool(tmp_path)
    invalid = call_find(tool, {'pattern': '['})
    assert invalid.status == 'error'
    assert 'glob' in invalid.content
    for index in range(230):
        (tmp_path / f'{index:03d}-{"x" * 235}.md').write_text('x')
    bounded = call_find(tool, {'pattern': '*.md'})
    assert bounded.status == 'success'
    assert len(bounded.content.encode('utf-8')) <= 50 * 1024 + 100
    assert 'truncated' in bounded.content.lower()
