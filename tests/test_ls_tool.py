"""Pi-shaped directory listing with the project's workspace boundary."""

from native_application_helpers import async_test

import pytest
from langchain_core.messages import ToolMessage
from tool_feedback_helpers import invoke_tool_feedback

from naiyue_test_agent.tools import ls as ls_module
from naiyue_test_agent.tools.ls import create_session_ls_tool


async def call_ls(tool, args, root):
    return (await invoke_tool_feedback(tool, args, context={'workspace_root': root}, call_id='ls-call'))


@async_test
async def test_ls_default_and_selected_directory_are_sorted_and_mark_directories(tmp_path):
    (tmp_path / 'z.txt').write_text('z')
    (tmp_path / 'Alpha.txt').write_text('a')
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'nested.txt').write_text('n')
    tool = create_session_ls_tool()

    assert set(tool.tool.tool_call_schema.model_fields) == {'path', 'limit'}
    assert (await call_ls(tool, {}, tmp_path)).content == 'Alpha.txt\nsub/\nz.txt'
    assert (await call_ls(tool, {'path': 'sub'}, tmp_path)).content == 'nested.txt'
    assert (await call_ls(tool, {'path': str(tmp_path / 'sub')}, tmp_path)).content == 'nested.txt'


@async_test
async def test_ls_empty_and_bounded_results(tmp_path):
    tool = create_session_ls_tool()
    assert (await call_ls(tool, {}, tmp_path)).content == '(empty directory)'
    for name in ('a.txt', 'b.txt', 'c.txt'):
        (tmp_path / name).write_text(name)
    limited = (await call_ls(tool, {'limit': 2}, tmp_path))
    assert limited.status == 'success'
    assert limited.content.startswith('a.txt\nb.txt\n')
    assert 'truncated' in limited.content.lower()
    assert 'c.txt' not in limited.content


@async_test
@pytest.mark.parametrize('args', [
    {'path': ''}, {'path': 4}, {'limit': 0}, {'limit': -1},
    {'limit': 1.5}, {'limit': 2001}, {'other': 'PRIVATE_ARGUMENT'},
])
async def test_ls_invalid_arguments_return_actionable_error(tmp_path, args):
    result = (await call_ls(create_session_ls_tool(), args, tmp_path))
    assert result.status == 'error'
    assert 'ls' in result.content and 'path' in result.content and 'limit' in result.content
    assert 'PRIVATE_ARGUMENT' not in str(result)


@async_test
async def test_ls_shows_hidden_sensitive_and_external_symlink_names(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'visible.md').write_text('visible')
    (root / '.env').write_text('SECRET')
    (root / '.private').mkdir()
    (root / '.gitignore').write_text('*.pyc')
    (root / '.github').mkdir()
    (root / '.github' / 'workflow.yml').write_text('name: test')
    (root / 'private.key').write_text('SECRET')
    (root / 'cert.pem').write_text('SECRET')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'external-link').symlink_to(outside, target_is_directory=True)
    (root / 'secret-alias').symlink_to(root / '.env')
    (root / 'broken-link').symlink_to(root / 'missing')

    result = (await call_ls(create_session_ls_tool(), {}, root))

    assert result.status == 'success'
    lines = result.content.splitlines()
    assert lines[0] == 'visible.md'
    assert lines[1].startswith('[ls: not listed: ')
    for name in ('.env', '.github', '.gitignore', '.private', 'cert.pem',
                 'external-link', 'private.key', 'secret-alias', 'broken-link'):
        assert name + ': ' in lines[1]
    hidden = (await call_ls(create_session_ls_tool(), {'path': '.github'}, root))
    assert hidden.status == 'error' and 'NoAsk' in hidden.content



@async_test
async def test_ls_rejects_hidden_traversal_missing_file_and_outside_without_approval(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'ordinary.txt').write_text('x')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'PRIVATE_NAME').write_text('x')
    tool = create_session_ls_tool()

    for path in ('../outside', '.env', '.hidden', 'missing', 'ordinary.txt'):
        result = (await call_ls(tool, {'path': path}, root))
        assert result.status == 'error'
        assert 'PRIVATE_NAME' not in str(result)
    external = (await call_ls(tool, {'path': str(outside)}, root))
    assert external.status == 'error'
    assert external.content == 'Tool was not executed: access denied by the permission policy (NoAsk).'
    assert 'PRIVATE_NAME' not in str(external)


@async_test
async def test_ls_escapes_control_characters_in_names(tmp_path):
    (tmp_path / 'multi\nline.txt').write_text('x')
    result = (await call_ls(create_session_ls_tool(), {}, tmp_path))
    assert result.status == 'success'
    assert result.content == 'multi\\nline.txt'


@async_test
async def test_ls_byte_budget_truncates_before_large_name(tmp_path):
    # 250 UTF-8 bytes per name keeps filesystem component sizes valid.
    for index in range(220):
        (tmp_path / f'{index:03d}-{"a" * 235}').write_text('x')
    result = (await call_ls(create_session_ls_tool(), {'limit': 500}, tmp_path))
    assert result.status == 'success'
    assert len(result.content.encode('utf-8')) <= 50 * 1024 + 100
    assert 'truncated' in result.content.lower()


@async_test
async def test_ls_io_and_unexpected_failures_are_safe_tool_results(monkeypatch, tmp_path, caplog):
    tool = create_session_ls_tool()

    def unavailable(_path):
        raise OSError('PRIVATE_INTERNAL_PATH')

    monkeypatch.setattr(ls_module.os, 'scandir', unavailable)
    failed_io = (await call_ls(tool, {}, tmp_path))
    assert failed_io.status == 'error'
    assert 'I/O error' in failed_io.content
    assert 'PRIVATE_INTERNAL_PATH' not in str(failed_io) + caplog.text

    def unexpected(_path):
        raise RuntimeError('PRIVATE_INTERNAL_PATH')

    monkeypatch.setattr(ls_module.os, 'scandir', unexpected)
    failed_unknown = (await call_ls(tool, {}, tmp_path))
    assert failed_unknown.status == 'error'
    assert 'Tool execution failed (RuntimeError)' in failed_unknown.content
    assert 'PRIVATE_INTERNAL_PATH' not in str(failed_unknown) + caplog.text
