"""Find returns bounded names while respecting the search workspace."""

from native_application_helpers import async_test

import pytest
from langchain_core.messages import ToolMessage
from tool_feedback_helpers import invoke_tool_feedback

from naiyue_test_agent.tools import find as find_module
from naiyue_test_agent.tools.find import create_session_find_tool


async def call_find(tool, args, root):
    return (await invoke_tool_feedback(tool, args, context={'workspace_root': root}, call_id='find-call'))


@async_test
async def test_find_glob_paths_hidden_names_and_gitignore(tmp_path):
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
    tool = create_session_find_tool()

    assert set(tool.tool.tool_call_schema.model_fields) == {'pattern', 'path', 'limit', 'timeout'}
    result = (await call_find(tool, {'pattern': '*.md'}, tmp_path))
    assert result.status == 'success'
    assert result.content == 'root.md\nsrc/inner.md\n[find: not searched: .hidden.md: unapproved_private_or_external]'
    assert (await call_find(tool, {'pattern': '*.md', 'path': 'src'}, tmp_path)).content == 'inner.md'
    assert (await call_find(tool, {'pattern': 'src/**/*.py'}, tmp_path)).content == (
        'src/code.py\nsrc/nested/deep/more.py'
    )
    assert (await call_find(tool, {'pattern': '**/*.py'}, tmp_path)).content == (
        'root.py\nsrc/code.py\nsrc/nested/deep/more.py'
    )


@async_test
async def test_find_empty_and_result_limit(tmp_path):
    for name in ('a.py', 'b.py', 'c.py'):
        (tmp_path / name).write_text(name)
    tool = create_session_find_tool()
    assert (await call_find(tool, {'pattern': '*.txt'}, tmp_path)).content == (
        'No files found matching pattern'
    )
    limited = (await call_find(tool, {'pattern': '*.py', 'limit': 2}, tmp_path))
    assert limited.status == 'success'
    names = limited.content.split('\n[find:')[0].splitlines()
    assert len(names) == 2 and set(names) <= {'a.py', 'b.py', 'c.py'}
    assert names == sorted(names, key=lambda name: (name.casefold(), name))
    assert 'truncated' in limited.content.lower()
    assert 'only the collected subset is sorted' in limited.content


@async_test
@pytest.mark.parametrize('args', [
    {}, {'pattern': ''}, {'pattern': 3}, {'pattern': '*.py', 'path': ''},
    {'pattern': '*.py', 'limit': 0}, {'pattern': '*.py', 'limit': 2001},
    {'pattern': '*.py', 'extra': 'PRIVATE_ARGUMENT'},
])
async def test_find_schema_errors_are_safe(tmp_path, args):
    result = (await call_find(create_session_find_tool(), args, tmp_path))
    assert result.status == 'error'
    assert 'pattern' in result.content and 'limit' in result.content
    assert 'PRIVATE_ARGUMENT' not in str(result)


@async_test
async def test_find_rejects_traversal_non_directory_and_outside_without_grant(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'ordinary.txt').write_text('x')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'PRIVATE.md').write_text('x')
    tool = create_session_find_tool()
    for path in ('../outside', 'ordinary.txt', 'missing'):
        result = (await call_find(tool, {'pattern': '*', 'path': path}, root))
        assert result.status == 'error'
        assert 'PRIVATE.md' not in str(result)
    external = (await call_find(tool, {'pattern': '*', 'path': str(outside)}, root))
    assert external.status == 'error'
    assert external.content == 'Tool was not executed: access denied by the permission policy (NoAsk).'


@async_test
async def test_find_missing_fd_is_actionable_tool_error(monkeypatch, tmp_path):
    monkeypatch.setattr(find_module, 'resolve_tool_binary', lambda _name: None)
    result = (await call_find(create_session_find_tool(), {'pattern': '*.md'}, tmp_path))
    assert result.status == 'error'
    assert 'fd' in result.content
    assert 'install' in result.content.lower()


@async_test
async def test_find_invalid_glob_and_output_budget(tmp_path):
    tool = create_session_find_tool()
    invalid = (await call_find(tool, {'pattern': '['}, tmp_path))
    assert invalid.status == 'error'
    assert 'glob' in invalid.content
    for index in range(230):
        (tmp_path / f'{index:03d}-{"x" * 235}.md').write_text('x')
    bounded = (await call_find(tool, {'pattern': '*.md'}, tmp_path))
    assert bounded.status == 'success'
    assert len(bounded.content.encode('utf-8')) <= 50 * 1024 + 100
    assert 'truncated' in bounded.content.lower()
