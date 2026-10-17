"""Workspace Bash output ownership, limits, and explicit cleanup."""

from native_application_helpers import async_test


import pytest


@async_test
async def test_clear_only_after_preview_and_preserves_other_files(monkeypatch, tmp_path):
    from naiyue_test_agent.tools.output import (
        clear_shell_outputs, preview_shell_outputs,
    )
    from tests.test_host_bash import make_app, bash_results, _archive_path

    app = await make_app(monkeypatch, "python -c \"print('A'*70000)\"")
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    path = tmp_path / _archive_path(bash_results(result)[0].content, 'raw')
    outside = tmp_path / 'user-file.txt'
    outside.write_text('keep')

    plan = preview_shell_outputs(tmp_path)
    assert plan.file_count == 1
    assert plan.total_bytes == 70001
    assert path.exists()
    assert clear_shell_outputs(plan) == (1, 70001)
    assert not path.exists()
    assert outside.read_text() == 'keep'
    assert (tmp_path / 'naiyue-agent-output' / '.gitignore').exists()


@async_test
async def test_clear_refuses_changed_plan_and_unexpected_file(monkeypatch, tmp_path):
    from naiyue_test_agent.tools.output import (
        clear_shell_outputs, preview_shell_outputs,
    )
    from tests.test_host_bash import make_app, bash_results, _archive_path

    app = await make_app(monkeypatch, "python -c \"print('A'*70000)\"")
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    path = tmp_path / _archive_path(bash_results(result)[0].content, 'raw')
    plan = preview_shell_outputs(tmp_path)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        clear_shell_outputs(plan)
    assert path.read_bytes() == b'changed'

    unknown = tmp_path / 'naiyue-agent-output' / 'important.txt'
    unknown.write_text('keep')
    with pytest.raises(ValueError, match='unexpected'):
        preview_shell_outputs(tmp_path)
    assert unknown.read_text() == 'keep'


@async_test
async def test_clear_rejects_symlink_in_output_directory(monkeypatch, tmp_path):
    from naiyue_test_agent.tools.output import preview_shell_outputs
    from tests.test_host_bash import make_app

    app = await make_app(monkeypatch, "python -c \"print('A'*70000)\"")
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    await app.aresume_tool_approval(session, ['approve'])
    outside = tmp_path / 'outside.txt'
    outside.write_text('keep')
    (tmp_path / 'naiyue-agent-output' / ('f' * 32 + '-stderr.log')).symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        preview_shell_outputs(tmp_path)
    assert outside.read_text() == 'keep'


@async_test
async def test_cli_clear_needs_confirmation(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent import application
    from naiyue_test_agent.application import cli
    from naiyue_test_agent.model_access import ModelCatalog
    from tests.test_host_bash import BashFake, make_app, bash_results, _archive_path
    from langchain_core.messages import AIMessage

    app = await make_app(monkeypatch, "python -c \"print('A'*70000)\"")
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    path = tmp_path / _archive_path(bash_results(result)[0].content, 'raw')
    model = BashFake(responses=[AIMessage(content='unused')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    entries = iter((':outputs clear', ':outputs clear confirm', ':quit'))

    def terminal_input(_prompt=''):
        line = next(entries)
        if line == ':outputs clear confirm':
            assert path.exists()
        return line

    monkeypatch.setattr('builtins.input', terminal_input)

    assert cli.main(['--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert '1 个文件' in output
    assert '确认' in output
    assert not path.exists()


def test_capture_error_cannot_mark_partial_artifact_complete(tmp_path):
    from naiyue_test_agent.tools.output import ShellOutputArchive
    from naiyue_test_agent.tools import BashSettings, ToolOutputSettings

    archive = ShellOutputArchive(tmp_path, BashSettings(), ToolOutputSettings())
    archive.record(b'A' * 70000, activate=True)
    archive.mark_incomplete('capture_incomplete')
    result = archive.finish()
    assert result.paths['raw'].startswith('naiyue-agent-output/')
    assert result.complete is False
    assert result.reason == 'capture_incomplete'


@async_test
async def test_nonzero_bash_still_returns_saved_output_path(monkeypatch, tmp_path):
    from tests.test_host_bash import make_app, bash_results, _archive_path

    app = await make_app(monkeypatch, "python -c \"print('F'*70000); raise SystemExit(7)\"")
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert message.status == 'error'
    assert 'exit_code=7' in message.content
    assert 'archive_complete=true' in message.content
    assert (tmp_path / _archive_path(message.content, 'raw')).read_text().startswith('F' * 70000)
